import traceback
import time

import numpy as np
from sklearn import svm
from sklearn.utils import shuffle
import matplotlib.pyplot as plt

from ..data.mfsc import result_handler
from ..data.io import load_labels_from_mat
from ..generic import ProgressNotifier

class Trainer():
    """ 
    Trainer allows to easily train a model on a dataset provided through
    a path variable upon initialization. It reads the data and stores it in
    itself, as to enable easy obtaining of single datapoints with trainer.next()
    """

    def __init__(self, datapath, labelpath, validation_split=0.2):
        """ Initialize the trainer with path to data stored on device """
        self.datapath = datapath
        self.labelpath = labelpath

        self.valsplit = validation_split

        self.trainindex = 0
        self.valindex = 0

        self.read_data()

        self.train_prog = ProgressNotifier(
            title='Training', total=self.trainsize)
        self.val_prog = ProgressNotifier(
            title='Validating', total=self.valsize, show_bar=False)

    def read_data(self):
        """ Read the data from storage. Get size of the dataset (i.e. number
        of datapoints) and shape of a single datapoint that may be accessed
        from outside. """

        data = result_handler().load_file(self.datapath)
        labels = load_labels_from_mat(self.labelpath)
        data, labels = shuffle(data, labels, random_state=0)

        assert data.shape[0] == labels.shape[0], \
            "Data and labels do not fit in shape"

        # TODO This is only a temporary hard coded fix, because the data are
        # currently provided in a transposed manner. Hence, we need to trans-
        # pose them back
        new_data = np.empty((data.shape[0], data.shape[2], data.shape[1]))
        for i in range(data.shape[0]):
            new_data[i] = data[i].T
        data = new_data

        # Get data shape
        self.datashape = (data.shape[1], data.shape[2])
        print("Read {} datapoints from storage with shape {}x{}"
            .format(data.shape[0], data.shape[1], data.shape[2]))

        # Get size of data and compute size of validation and training set 
        # from provided validation split
        datasize = data.shape[0]
        self.valsize = int(datasize * self.valsplit)
        self.trainsize = datasize - self.valsize

        # Randomly choose indices for validation and training set 
        # corresponding to previously defined sizes
        val_indices = np.random.choice(
            data.shape[0], self.valsize, replace=False)
        train_indices = np.delete(np.arange(datasize), val_indices)
        
        # Get the data and labels
        self.valdata = data[val_indices]
        self.traindata = data[train_indices]
        self.vallabels = labels[val_indices]
        self.trainlabels = labels[train_indices]

    def next(self):
        """ Get the datapoint for the training data at the current index and 
        increase the index. If the index has reached the end of the dataset, 
        raise an IndexError and notify that index has to be reset. """

        # Fail safe if index has reached end of dataset
        try:
            # Draw the current image
            image = self.traindata[self.trainindex]
        except IndexError:
            raise IndexError("Trainer reached the end of the training data. To use further, call `trainer.reset()` to reset the index to 0.")

        # Increase the index
        self.trainindex += 1
                
        return image

    def valnext(self):
        """ Get the datapoint for the validation data at the current index and 
        increase the index. If the index has reached the end of the dataset, 
        raise an IndexError and notify that index has to be reset. """

        # Fail safe if index has reached end of dataset
        try:
            # Draw the current image
            image = self.valdata[self.valindex]
        except IndexError:
            raise IndexError("Trainer reached the end of the validation data. To use further, call `trainer.reset()` to reset the index to 0.")

        # Increase the index
        self.valindex += 1

        return image

    def reset(self):
        """ Reset indexes to zero and reset the progress notifiers. """
        self.trainindex = 0
        self.valindex = 0
        
        self.train_prog.reset()
        self.val_prog.reset()

    def set_model(self, model):
        """ Set the model instance for fitting. Has to be done 
        before calling `self.fit()` """

        if not self.datashape == model.input_layer.input_shape:
            raise ValueError("The data in the trainer has a different shape than what this model was initialized for. Data shape: {}, Model shape: {}"
                .format(self.datashape, model.input_layer.input_shape))

        self.model = model

    def fit(self, epochs):
        """ Fit the model """

        if not self.model:
            raise ValueError("Model is not set. Call `trainer.set_model()` with an appropriate model instance.")
        
        print("Fitting model on {} images, validating on {} images"
            .format(self.trainsize, self.valsize))

        # Check if weights are frozen
        if not self.model.conv_layer.is_training:
            self.model.conv_layer.is_training = True
            print("WARNING: model weights were automatically unfrozen")

        # Collect the membrane potentials of the pooling layer for all images
        # in all epochs
        train_potentials = np.empty((
            epochs, 
            self.trainsize, 
            self.model.pooling_layer.output_shape[0], 
            self.model.pooling_layer.output_shape[1]))
        val_potentials = np.empty((
            epochs, 
            self.valsize, 
            self.model.pooling_layer.output_shape[0], 
            self.model.pooling_layer.output_shape[1]))

        # Keep track of feature map activations
        feature_map_activations = []
        # Iterate through all epochs
        for epoch in range(epochs):
            print("\nEpoch {}/{}".format(epoch+1, epochs))
            start_time = time.time()

            # Reset the trainer at the start of each epoch (i.e. index = 0)
            # Also resets progress notifiers
            self.reset()

            test_freq = 100

            # TRAIN on the training data
            self.model.unfreeze()
            score = 'Nan'
            train_scores = []
            for i in range(self.trainsize):
                train_potentials[epoch,i] = self.model(self.next())
                if (epoch * self.trainsize + i + 1) % 1 == 0:
                    feature_map_activations.append(self.model.conv_layer.weights[0, 0:3, :, :])
                if i == 10:
                    break
                if (i+1) % test_freq == 0:
                    clf = svm.SVC()
                    clf = clf.fit(
                        train_potentials[epoch, i-(test_freq-1):i+1]
                            .reshape(test_freq,9*50), 
                        self.trainlabels[i-(test_freq-1):i+1])
                    score = clf.score(
                        train_potentials[epoch, i-(test_freq-1):i+1]
                            .reshape(test_freq,9*50), 
                        self.trainlabels[i-(test_freq-1):i+1])
                    train_scores.append(score)
                self.train_prog.update({'Accuracy':score})
            self.train_prog.update({'Mean Accuracy':np.mean(train_scores)})
            print()
            break

            # VALIDATE on the validation data
            score = 'Nan'
            val_scores = []
            self.model.freeze()
            for i in range(self.valsize):
                val_potentials[epoch,i] = self.model(self.valnext())
                if (i+1) % test_freq == 0:
                    clf = svm.SVC()
                    clf = clf.fit(
                        val_potentials[epoch, i-(test_freq-1):i+1]
                            .reshape(test_freq,9*50), 
                        self.vallabels[i-(test_freq-1):i+1])
                    score = clf.score(
                        val_potentials[epoch, i-(test_freq-1):i+1]
                            .reshape(test_freq,9*50), 
                        self.vallabels[i-(test_freq-1):i+1])
                    val_scores.append(score)
                self.val_prog.update({'Accuracy':score})
            self.val_prog.update({'Mean Accuracy':np.mean(val_scores)})
            self.model.unfreeze()

            # Print elapsed time
            end_time = time.time()
            elapsed_time = end_time-start_time
            print('\nElapsed time {:02}:{:02}:{:02}'.format(
                int(elapsed_time/60), 
                int(elapsed_time%60), 
                int(elapsed_time%60%1*100)))

        print("\nDone")
        self.visualize_featuremaps(feature_map_activations)
        return train_potentials, val_potentials

    def visualize_snn(self):
        """
        Plots the output of the SNN for a sample of each digit.
        """
        done = False
        index = 0
        labels_used = []
        uniques = list(set(self.trainlabels))
        fig, axs = plt.subplots(int(len(uniques)/2), 2)
        self.model.freeze()
        while not done:
            label = self.trainlabels[index]
            if not label in labels_used:
                image = self.traindata[index]
                axs[label].plot(self.model(image))
                labels_used.append(label)
            index += 1
            if labels_used == uniques:
                done = True
        plt.show()

    def visualize_featuremaps(self, activations):
        fig, axs = plt.subplots(len(activations), 3)
        for index, item in enumerate(activations):
            axs[index, 0].plot(item[0,:,:])
            axs[index, 1].plot(item[1,:,:])
            axs[index, 2].plot(item[2,:,:])
        plt.show()
